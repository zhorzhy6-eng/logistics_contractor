#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Виджет Максимилиана: иконка в правом нижнем углу, выезжающая панель,
плавающий кот (задание, часть D.1).

Три части, каждая включается настройками:

  * ``AgentIconButton`` — маленький кот в правом нижнем углу окна (D.1.1),
    в статус-баре. Клик открывает и закрывает панель;
  * ``AgentPanel`` — панель справа шириной ~350 px, выезжает анимацией
    200 мс (D.1.2): аватар, история диалога, поле ввода, «Отправить»;
  * ``FloatingCat`` — плавающий кот в правом нижнем углу (D.1.3):
    моргает, «думает», перетаскивается, клик открывает панель.

``install_agent(window)`` встраивает всё это в окно приложения и
возвращает ``AgentWidget`` — единственный объект, которым потом управляют
(настройки, горячая клавиша, закрытие).

Работа с моделью идёт в отдельном потоке (`AgentWorker` в `QThread`):
окно не должно замирать на время запроса. Подтверждение опасных действий
показывает `ConfirmationDialog` (задание, часть D.5).

Про потоки: агент внутри рабочего потока читает и заполняет форму. Так
сделано осознанно — операции занимают доли секунды (диалоги Qt в этом же
потоке), а держать два потока (сеть и форма) значило бы синхронизировать
доступ к виджетам. Известное ограничение записано в docs/STATE.md.
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from PyQt5.QtCore import (
    QEasingCurve, QEvent, QMetaObject, QObject, QPropertyAnimation, QSize, Qt,
    Q_ARG, QThread, QTimer, pyqtSignal, pyqtSlot,
)
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QTextEdit, QToolButton,
    QVBoxLayout, QWidget,
)

from core.agent.agent import Agent, AgentReply
from core.agent.memory import DEFAULT_MAX_HISTORY, AgentMemory
from core.agent.personas import DEFAULT_PERSONA, persona
from core.agent import proactivity
from core.agent.tools import AgentContext, Tool, active_window, set_context_window
from core.settings_service import AGENT_NAME, get_settings_service
from ui import theme
from ui.agent_avatar import CatAvatar, avatar_icon

logger = logging.getLogger("ui.agent_widget")

#: Ширина панели (задание, часть D.1.2).
PANEL_WIDTH = 350
#: Длительность выезда панели, мс (задание, часть D.1.2).
PANEL_ANIMATION_MS = 200
#: Размеры аватара: иконка, панель, плавающий кот (задание, часть D.2).
ICON_SIZE = 32
PANEL_AVATAR_SIZE = 64
FLOATING_SIZE = 96
#: Как часто проверять, есть ли что подсказать без вопроса (часть 4), мс.
#: Сама подсказка показывается не чаще, чем разрешает настройка
#: `proactive_interval` (по умолчанию 5 минут) — см. proactivity.py.
SUGGESTION_CHECK_MS = 5000
#: Пауза после действия оператора перед проверкой формы, мс: оператор
#: заполняет поля подряд, и подсказка посреди ввода только мешала бы.
SUGGESTION_IDLE_MS = 2000

#: Инструмент, для которого подтверждение показывается ОСОБЫМ диалогом —
#: таблицей «поле → значение → куда» с правкой значений (задание, часть 1).
#: Для остальных опасных действий остаётся обычный вопрос «Да / Нет».
FILL_PREVIEW_TOOL = "parse_and_fill"

#: Сколько рабочий поток ждёт ответа оператора на вопрос о подтверждении.
#: Оператор может думать сколько угодно — ждём долго; ограничение стоит
#: только чтобы закрытая панель не подвесила агента навсегда.
CONFIRM_WAIT_SECONDS = 1800.0

#: Сколько рабочий поток ждёт выполнения инструмента в потоке интерфейса.
#: Внутри бывают и сетевые проверки (DaData), поэтому запас большой.
GUI_CALL_WAIT_SECONDS = 300.0

#: Инструменты, которые ТРОГАЮТ ФОРМУ — то есть виджеты. Их вызов обязан
#: идти в потоке интерфейса: агент работает в рабочем потоке, а виджет,
#: созданный или изменённый из чужого потока, Qt роняет вместе с программой
#: («Cannot create children for a parent that is in a different thread»,
#: «Cannot set parent, new parent is in a different thread»). Проверено
#: стресс-тестом 15.10.2026: прогон умирал на автозаполнении (№83).
GUI_TOOLS = (
    "get_current_form", "get_form_state", "get_open_tabs", "get_current_window",
    "get_selected_row", "get_recent_actions", "validate_current_form",
    "check_required", "fill_field", "fill_from_entity", "fill_entity_fields",
    "parse_and_fill", "apply_recognized_fields", "suggest_next_field",
    "suggest_from_history", "warn_expiring", "diagnose",
)


class _PendingConfirmation:
    """
    Запрос подтверждения, который рабочий поток передал интерфейсу.

    Рабочий поток не может показать диалог (виджеты живут в главном) и не
    может просто «вызвать сигнал и пойти дальше» — ему нужен ОТВЕТ. Поэтому
    запрос кладётся сюда, главный поток показывает диалог и кладёт ответ,
    а рабочий ждёт на ``event``.
    """

    __slots__ = ("tool", "arguments", "event", "answer", "handled")

    def __init__(self, tool: Tool, arguments: Dict[str, Any]):
        import threading

        self.tool = tool
        self.arguments = arguments
        self.event = threading.Event()
        self.answer = False
        #: Диалог показан (нужно, чтобы отличить «отказ» от «никто не пришёл»).
        self.handled = False

    def answer_with(self, value: bool) -> None:
        """Главный поток кладёт ответ и будит рабочего."""
        self.answer = bool(value)
        self.handled = True
        self.event.set()

    def wait(self, timeout: float) -> bool:
        """Рабочий поток ждёт ответа (True — ответ пришёл)."""
        return bool(self.event.wait(timeout))


class _PendingGuiCall:
    """
    Вызов инструмента, который рабочий поток передал интерфейсу.

    Устроен как ``_PendingConfirmation``: рабочий ждёт на ``threading.Event``,
    поток интерфейса выполняет вызов и кладёт результат (или исключение).
    """

    __slots__ = ("function", "arguments", "event", "result", "error")

    def __init__(self, function: Any, arguments: Dict[str, Any]):
        import threading

        self.function = function
        self.arguments = arguments
        self.event = threading.Event()
        self.result: Any = None
        self.error: Optional[BaseException] = None

    def finish(self, result: Any) -> None:
        """Поток интерфейса кладёт результат и будит рабочего."""
        self.result = result
        self.event.set()

    def fail(self, error: BaseException) -> None:
        """Поток интерфейса кладёт ошибку и будит рабочего."""
        self.error = error
        self.event.set()

    def wait(self, timeout: float) -> bool:
        return bool(self.event.wait(timeout))


class _GuiTool:
    """
    Инструмент, который выполняется в ПОТОКЕ ИНТЕРФЕЙСА.

    Обёртка вместо правки самих инструментов: `core/agent/tools.py` про Qt не
    знает и знать не должен, а решение «этот инструмент трогает виджеты»
    принимает интерфейс (см. ``GUI_TOOLS``).
    """

    def __init__(self, widget: "AgentWidget", name: str, function: Any):
        self.widget = widget
        self.name = name
        self.function = function

    def __call__(self, **arguments: Any) -> Any:
        return run_in_gui_thread(self.widget, self.function, arguments)

    def __repr__(self) -> str:  # pragma: no cover — для отладки
        return f"<GuiTool {self.name}>"


def run_in_gui_thread(widget: Any, function: Any,
                      arguments: Dict[str, Any]) -> Any:
    """
    Выполняет функцию в потоке интерфейса и возвращает её результат.

    Из потока интерфейса функция вызывается сразу. Из рабочего (там работает
    агент) — передаётся сигналом ``gui_call_requested``; рабочий ЖДЁТ, пока
    главный поток её выполнит, и получает результат или исключение.

    :raises RuntimeError: если поток интерфейса не ответил за отведённое
        время (окно закрыто) — инструмент сообщит об этом модели, а не
        подвесит программу навсегда.
    """
    if widget.is_gui_thread():
        return function(**arguments)

    pending = _PendingGuiCall(function, arguments)
    widget._gui_call = pending
    widget.gui_call_requested.emit()
    if not pending.wait(GUI_CALL_WAIT_SECONDS):
        widget._gui_call = None
        raise RuntimeError(
            "интерфейс не ответил на вызов инструмента за "
            f"{GUI_CALL_WAIT_SECONDS:.0f} с"
        )
    if pending.error is not None:
        raise pending.error
    return pending.result


def ask_in_gui_thread(widget: Any, tool: Tool,
                      arguments: Dict[str, Any]) -> bool:
    """
    Спрашивает оператора, показывая диалог В ПОТОКЕ ИНТЕРФЕЙСА.

    Зачем отдельная функция. Агент работает в рабочем потоке, а диалог —
    виджет. Создание виджета в рабочем потоке (да ещё с родителем из
    главного) роняет процесс: Qt отвечает «QObject::setParent: Cannot set
    parent, new parent is in a different thread» и завершает программу
    аварийно. Проверено 15.10.2026 на прогоне 200 запросов: три падения
    подряд на запросе «Перевозчик: ООО «Ромашка»…» (инструмент
    ``parse_and_fill`` — он единственный спрашивает подтверждение диалогом
    превью).

    Как устроено: из главного потока диалог показывается сразу; из рабочего
    запрос уходит сигналом ``confirmation_requested``, рабочий ждёт на
    ``threading.Event``, а главный поток показывает диалог и кладёт ответ.

    :return: ответ оператора (False — отказ, ошибка или интерфейса рядом нет).
    """
    if _current_thread() is _gui_thread():
        return bool(widget._confirm_in_gui_thread(tool, arguments))

    pending = _PendingConfirmation(tool, arguments)
    widget._confirmation = pending
    widget.confirmation_requested.emit()
    if not pending.wait(CONFIRM_WAIT_SECONDS):
        logger.warning(
            "Ответа на подтверждение (%s) не дождались за %.0f с — действие "
            "отменено", tool.name, CONFIRM_WAIT_SECONDS,
        )
        return False
    if not pending.handled:
        return False
    return bool(pending.answer)


def _gui_thread() -> Any:
    """Поток интерфейса (поток, в котором живёт QApplication)."""
    app = QApplication.instance()
    thread = getattr(app, "thread", None) if app is not None else None
    return thread() if thread is not None else None


def _current_thread() -> Any:
    """Текущий поток Qt."""
    from PyQt5.QtCore import QThread

    return QThread.currentThread()


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


class AgentRequest:
    """Один запрос к агенту: агент, сообщение и признак отмены."""

    def __init__(self, agent: Agent, message: str):
        self.agent = agent
        self.message = message
        #: Запрос отменили (выход из программы): результат уже не нужен.
        self.cancelled = False


class AgentWorker(QObject):
    """
    Работа с агентом в отдельном потоке.

    Поток — не пул: `QThread` агент создаёт, останавливает и дожидается
    САМ. Пул (`QThreadPool`) для одного длинного запроса оказался хуже:
    он живёт до разрушения окна, а окно Qt удаляет отложенно, поэтому
    пул с потоками доживал до завершения интерпретатора и ронял процесс
    (`0xC0000005`; проверено A/B-пробой tests/_tmp/_probe_exit_ab.py —
    с агентом выход падал 4 раза из 4, без агента — 4 из 4 чистых).
    """

    #: Работа закончена: текст ответа, инструменты, достигнут ли лимит шагов.
    finished = pyqtSignal(str, list, bool)
    #: Запрос не удался: текст ошибки.
    failed = pyqtSignal(str)
    #: Промежуточный шаг («Выполняю: поиск водителя»).
    activity = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._request: Optional[AgentRequest] = None

    @pyqtSlot(object)
    def process(self, request: AgentRequest) -> None:
        """Выполняет запрос и сообщает результат сигналами."""
        try:
            reply: AgentReply = request.agent.send(request.message)
        except Exception as e:  # noqa: BLE001 — сеть не должна ронять поток
            logger.exception("Запрос к Максимилиану не удался")
            if not request.cancelled:
                self.failed.emit(f"{type(e).__name__}: {e}")
            return
        if request.cancelled:
            logger.info("Ответ Максимилиана получен после отмены — не отдаём")
            return
        self.finished.emit(
            reply.text, list(reply.tool_names), bool(reply.step_limit_reached)
        )


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
    """Маленький кот в правом нижнем углу окна: клик открывает панель."""

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

    РОДИТЕЛЬ ОБЯЗАТЕЛЕН. Без родителя кот — самостоятельное окно верхнего
    уровня: главное окно закрывается, а он остаётся висеть на экране, пока
    жив процесс (баг #18). Поэтому `parent` передаётся ЯВНО и по имени
    (`FloatingCat(persona_key=…, parent=window)`): позиционный вызов
    `FloatingCat(persona_key)` попадал в `parent` и родителя не ставил.
    """

    #: Кот нажат (открыть панель).
    clicked = pyqtSignal()

    def __init__(self, persona_key: str = DEFAULT_PERSONA,
                 parent: Optional[QWidget] = None):
        if parent is None:
            # Ошибку видно сразу и в логе: молча созданное окно верхнего
            # уровня — это ровно тот баг, который здесь и закрыт.
            logger.error(
                "Плавающий кот создан БЕЗ родителя — он не закроется вместе "
                "с окном. Передайте parent=window."
            )
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
        #: Кот закрыт по-настоящему (нужно панели и тестам).
        self._closed = False

    def close_cat(self) -> None:
        """
        Закрывает кота по-настоящему: останавливает анимацию и прячет окно.

        Вызывается при закрытии окна программы (`force_close` →
        `AgentWidget.shutdown`) — кот не должен пережить программу. Метод
        идемпотентен: повторный вызов ничего не делает.
        """
        if self._closed:
            return
        self._closed = True
        try:
            self.avatar.stop_animation()
        except RuntimeError:
            pass
        self.hide()
        self.close()
        logger.info("Плавающий кот закрыт вместе с окном")

    @property
    def is_closed(self) -> bool:
        """Кот закрыт вместе с окном программы?"""
        return self._closed

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
        """
        Ставит кота в правый нижний угол окна (или экрана).

        Кот поднимается НАД статус-баром: в его правом краю живёт иконка
        Максимилиана, и накладываться на неё кот не должен (F.3).
        """
        reserved = 0
        if window is not None and window.isVisible():
            frame = window.frameGeometry()
            reserved = _status_bar_height(window)
        else:
            screen = QApplication.primaryScreen()
            frame = screen.availableGeometry() if screen else None
        if frame is None:
            return
        self.move(
            frame.right() - FLOATING_SIZE - 16,
            frame.bottom() - FLOATING_SIZE - 16 - reserved,
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
    #: Оператор просит открыть диагностику программы (задание, часть 3).
    diagnostics_requested = pyqtSignal()
    #: Оператор включил/выключил режим «Обучение» (подсказки на каждом шаге).
    tutorial_toggled = pyqtSignal(bool)

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

        #: Кнопка диагностики (задание, часть 3, пункт A.3): панель — то
        #: место, куда оператор приходит сказать «сломалось».
        self.btn_diagnostics = QToolButton()
        self.btn_diagnostics.setText("🔧")
        self.btn_diagnostics.setObjectName("agentToolButton")
        self.btn_diagnostics.setToolTip(
            "Диагностика программы: проверить состояние и собрать отчёт"
        )
        self.btn_diagnostics.setAutoRaise(True)
        self.btn_diagnostics.clicked.connect(self.diagnostics_requested)
        row.addWidget(self.btn_diagnostics)

        #: Кнопка режима «Обучение» (задание: система обучения): подсказки
        #: на каждом шаге заполнения. Состояние хранится в настройках.
        self.btn_tutorial = QToolButton()
        self.btn_tutorial.setText("🎓")
        self.btn_tutorial.setObjectName("agentToolButton")
        self.btn_tutorial.setToolTip("Обучение: подсказки на каждом шаге заполнения")
        self.btn_tutorial.setAutoRaise(True)
        self.btn_tutorial.setCheckable(True)
        self.btn_tutorial.toggled.connect(self.tutorial_toggled)
        row.addWidget(self.btn_tutorial)

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

    def set_tutorial_mode(self, enabled: bool) -> None:
        """Ставит кнопку «Обучение» в нужное состояние, не стреляя сигналом."""
        if self.btn_tutorial.isChecked() != bool(enabled):
            self.btn_tutorial.blockSignals(True)
            self.btn_tutorial.setChecked(bool(enabled))
            self.btn_tutorial.blockSignals(False)

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
    #: Что показывается при попытке отправить пустое сообщение.
    EMPTY_INPUT_HINT = "Введите запрос."

    def emit_submitted(self) -> None:
        """
        Отправляет текст из поля ввода.

        Пустое сообщение НЕ уходит и НЕ молчит (проблема #15): раньше
        нажатие «Отправить» с пустым полем не делало ничего, и оператор
        не понимал, сломалось ли что-то. Теперь панель показывает
        «Введите запрос.» и возвращает фокус в поле.
        """
        text = self.input.toPlainText().strip()
        if not text:
            self.set_status(self.EMPTY_INPUT_HINT)
            self.input.setFocus()
            return
        self.input.clear()
        self.set_status("")
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

    #: Просьба агента открыть диалог диагностики (задание, часть 2, пункт B).
    #: Инструмент `diagnose` работает в РАБОЧЕМ потоке, а виджеты можно
    #: создавать только в потоке интерфейса, поэтому просьба идёт сигналом:
    #: он доставляется в очередь главного потока и там открывает диалог.
    diagnostics_requested = pyqtSignal()

    #: Шаг агента («Выполняю: поиск водителя») — тоже из РАБОЧЕГО потока.
    #: Сигнал, а не вызов панели на месте: см. on_activity_reported —
    #: это была причина падений прогона 15.10.2026.
    activity_reported = pyqtSignal(str)

    #: Просьба показать диалог подтверждения (агент работает в рабочем
    #: потоке, диалог строится в главном — см. ask_in_gui_thread).
    confirmation_requested = pyqtSignal()

    #: Просьба выполнить инструмент, который трогает форму (виджеты можно
    #: менять только в потоке интерфейса — см. run_in_gui_thread).
    gui_call_requested = pyqtSignal()

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
        #: Опознавательный знак потока интерфейса. Сравнение именно по
        #: ``threading.get_ident()``: обёртки QThread у PyQt сравнивать
        #: ненадёжно, а виджет ВСЕГДА создаётся в потоке интерфейса.
        import threading

        self._gui_thread_id = threading.get_ident()

        #: Подписка на шаги агента — здесь, а не в _ensure_panel: сигнал
        #: должен быть связан ДО первого запроса, иначе первые шаги пропали
        #: бы, а вызов панели остался бы в рабочем потоке.
        self.activity_reported.connect(self.on_activity_reported)
        #: Запрос подтверждения из рабочего потока: диалог показывает
        #: главный поток (см. on_confirmation_requested).
        self._confirmation: Optional[_PendingConfirmation] = None
        self.confirmation_requested.connect(self.on_confirmation_requested)
        #: Вызов инструмента формы из рабочего потока: выполняет главный
        #: поток (см. on_gui_call_requested).
        self._gui_call: Optional[_PendingGuiCall] = None
        self.gui_call_requested.connect(self.on_gui_call_requested)

        self.panel: Optional[AgentPanel] = None
        self.icon_button: Optional[AgentIconButton] = None
        self.floating: Optional[FloatingCat] = None
        #: Горячая клавиша панели (QKeySequence). Оставлено публичным именем
        #: `shortcut` ради тестов и панели: раньше здесь лежал QShortcut,
        #: теперь — сочетание, а само нажатие ловит eventFilter.
        self.shortcut: Optional[QKeySequence] = None

        self._agent: Optional[Agent] = None
        #: Завершение уже выполнено (метод shutdown идемпотентен).
        self._shutdown_done = False
        #: Сочетание горячей клавиши (None — клавиша выключена).
        self._hotkey: Optional[QKeySequence] = None
        #: Текущий запрос к агенту (None — агент не думает).
        self._request: Optional[AgentRequest] = None
        #: Рабочий поток агента: создаётся при первом вопросе и живёт до
        #: выхода из программы (см. AgentWorker: почему QThread, а не пул).
        self._thread: Optional[QThread] = None
        self._worker: Optional[AgentWorker] = None
        self._busy = False

        self._animation = QPropertyAnimation(self)
        self._animation.setDuration(PANEL_ANIMATION_MS)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._on_panel_width)

        #: Подсказки без вопроса (задание, часть 4): движок решает «не рано
        #: ли снова», таймер периодически смотрит форму. Родитель таймеров —
        #: сам AgentWidget: они живут ровно столько же, сколько окно.
        self._suggestions: Optional[Any] = None
        #: Подсказки режима «Обучение» (система обучения): отдельный движок
        #: с нулевым интервалом — подсказка меняется на каждом шаге.
        self._tutorial_engine: Optional[Any] = None
        self._suggestion_timer = QTimer(self)
        self._suggestion_timer.setInterval(SUGGESTION_CHECK_MS)
        self._suggestion_timer.timeout.connect(self._check_suggestions)
        #: Тишина после последнего действия оператора, прежде чем
        #: подсказывать: во время заполнения формы подсказка мешает.
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(SUGGESTION_IDLE_MS)
        app_for_actions = QApplication.instance()
        actions_logger = getattr(app_for_actions, "_action_logger", None)
        if actions_logger is not None:
            actions_logger.action_recorded.connect(self._on_operator_action)

        #: Виджет окна, в котором сейчас лежит панель. Ставится ДО
        #: apply_settings: показ панели «при старте» вешает фильтр событий
        #: на этот виджет, и он должен быть определён заранее.
        self._host: Optional[QWidget] = None

        self.apply_settings()
        app = QApplication.instance()
        if app is not None:
            # Страховка на случай выхода, который не идёт через force_close()
            # окна. Подписка СНИМАЕТСЯ в shutdown(): живая связь с методом
            # Python, дошедшая до завершения интерпретатора, роняет процесс
            # (0xC0000005 — проверено пробой tests/_tmp/_probe_exit_launch.py,
            # это дословный сценарий tests/test_exit_clean.py).
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
        # Подсказки без вопроса (часть 4): только у включённого помощника.
        self._apply_proactive(values if enabled else {"proactive": False})
        # Режим «Обучение» (система обучения): подсказки на каждом шаге.
        self._apply_tutorial(values if enabled else {"tutorial_mode": False})

        if self.icon_button is not None:
            self.icon_button.setVisible(show_icon)

        # Панель: создаём при первой необходимости, дальше только правим.
        if show_panel_possible:
            self._ensure_panel(
                AGENT_NAME, persona_key,
                int(values["max_history"]), int(values["max_tool_steps"]),
            )
            if bool(values["show_panel_on_start"]) and not self.panel.isVisible():
                self.show_panel()
        elif self.panel is not None:
            self.panel.set_persona(persona_key)

        if self.panel is not None:
            self.panel.set_tutorial_mode(bool(values["tutorial_mode"]))

        logger.info(
            "Максимилиан: включён=%s, режим=%s, образ=%s, плавающий=%s",
            enabled, mode, persona_key, values["show_floating"],
        )

    def _ensure_icon(self, needed: bool, persona_key: str) -> None:
        """Создаёт иконку в правом нижнем углу окна, если её ещё нет."""
        if self.icon_button is None:
            if not needed:
                return
            self.icon_button = AgentIconButton(persona_key, self.window)
            self.icon_button.clicked.connect(self.toggle_panel)
            if not _place_icon(self.window, self.icon_button):
                # Статус-бара нет (нестандартный каркас окна) — иконка
                # переезжает в шапку: агент должен быть доступен.
                if not _place_icon_in_header(self.window, self.icon_button):
                    logger.warning("Не удалось разместить иконку Максимилиана")
        self.icon_button.set_persona(persona_key)

    def _ensure_floating(self, needed: bool, persona_key: str) -> None:
        """
        Создаёт плавающего кота, если он включён настройкой.

        Настройка ``show_floating = false`` означает, что кот НЕ создаётся
        вовсе: раньше он создавался и просто прятался, а «спрятанное» окно
        всё равно живёт и может всплыть (баг #18).

        Родитель — окно программы, и передаётся по имени: кот обязан
        закрыться вместе с ним, а не остаться на экране.
        """
        if self.floating is None:
            if not needed:
                return
            self.floating = FloatingCat(persona_key=persona_key,
                                        parent=self.window)
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
            self.panel.diagnostics_requested.connect(self.on_open_diagnostics)
            self.panel.tutorial_toggled.connect(self.on_tutorial_toggled)
            #: Просьба инструмента `diagnose` (из рабочего потока) открыть
            #: тот же диалог: связь сигналом — диалог создаётся в потоке
            #: интерфейса, а не в потоке агента.
            self.diagnostics_requested.connect(self.on_open_diagnostics)
            self._animation.setTargetObject(self.panel)
            self._animation.setPropertyName(b"maximumWidth")
            self._load_history(max_history, max_tool_steps)
        # Имя всегда одно и то же (AGENT_NAME): панель подписывается им
        # даже если в старом файле настроек лежало другое значение.
        self.panel.set_agent_name(AGENT_NAME)
        self.panel.set_persona(persona_key)

    def _apply_hotkey(self, enabled: bool, sequence: str) -> None:
        """
        Включает или выключает горячую клавишу панели.

        Клавиша обрабатывается ФИЛЬТРОМ СОБЫТИЙ, а не `QShortcut`. Причина
        практическая: `QShortcut` живёт отдельным объектом и на выходе из
        программы роняет процесс (`0xC0000005`, проверено пробой
        tests/_tmp/_probe_exit_matrix.py — с `QShortcut` падение повторялось
        в трёх запусках из трёх, без него выход чистый). Фильтр живёт в
        самом `AgentWidget`, никаких лишних объектов не создаёт и убирается
        вместе с окном.
        """
        if self._hotkey is not None:
            try:
                self.window.removeEventFilter(self)
            except (TypeError, RuntimeError):
                pass
            self._hotkey = None
            self.shortcut = None

        text = str(sequence or "").strip()
        if not enabled or not text:
            return

        key = QKeySequence(text)
        if key.isEmpty() or key.count() == 0:
            logger.warning("Горячая клавиша %r не разобрана — не подключаю", text)
            return

        self._hotkey = key
        #: То же сочетание под старым именем (тесты и панель).
        self.shortcut = QKeySequence(key)
        self.window.installEventFilter(self)
        logger.info("Максимилиан: горячая клавиша %s", text)

    def hotkey_sequence(self) -> QKeySequence:
        """Сочетание горячей клавиши (пустое — клавиша выключена)."""
        if self._hotkey is None:
            return QKeySequence()
        return QKeySequence(self._hotkey)

    def handle_hotkey(self) -> None:
        """
        Реакция на горячую клавишу: открыть или закрыть панель.

        Отдельный метод нужен тестам: они вызывают реакцию напрямую,
        не поднимая настоящее нажатие клавиши.
        """
        self.toggle_panel()

    def reload_settings(self) -> None:
        """Перечитывает настройки и применяет их (после диалога настроек)."""
        self.settings.reload()
        self.apply_settings()

    # ---------------------------------------------------------
    # Один разговор на всю программу (задание, часть 7)
    # ---------------------------------------------------------
    def sync_history(self) -> bool:
        """
        Подтягивает разговор, продолженный в ДРУГОМ окне программы.

        Максимилиан стоит в каждом окне, но разговор один: оператор
        спрашивает в Экспедиторстве, переключается в Аренду — и панель там
        должна показать ту же беседу, а не пустую (иначе выглядит так,
        будто помощник работает только в одном окне).

        Сверка идёт с базой (`AgentMemory.sync`) и только когда в другом
        окне действительно что-то добавилось; панель обновляется, если
        история изменилась.

        :return: True, если история подтянулась
        """
        if self._agent is None or self.panel is None:
            return False
        try:
            loaded = self._agent.memory.sync()
        except Exception as e:  # noqa: BLE001 — сверка не должна мешать работе
            logger.warning("История Максимилиана не синхронизирована: %s", e)
            return False
        if not loaded:
            return False
        self.panel.set_history(loaded)
        return True

    # ---------------------------------------------------------
    # Агент
    # ---------------------------------------------------------
    def _make_agent(self, max_history: int, max_tool_steps: int) -> Agent:
        """Создаёт агента: клиент, контекст окна, история диалога."""
        context = self._context()
        memory = AgentMemory(max_history=max_history)
        agent = Agent(
            client=None,
            context=context,
            memory=memory,
            persona_key=self.agent_settings()["avatar"],
            max_tool_steps=max_tool_steps,
            max_history=max_history,
        )
        self._route_gui_tools(agent)
        return agent

    def is_gui_thread(self) -> bool:
        """Мы в потоке интерфейса? (по нему решается, звать ли сигнал)."""
        import threading

        return threading.get_ident() == self._gui_thread_id

    def _route_gui_tools(self, agent: Agent) -> int:
        """
        Переводит инструменты формы на выполнение в потоке интерфейса.

        Агент работает в рабочем потоке, а инструменты вида ``get_form_state``
        и ``parse_and_fill`` читают и меняют ВИДЖЕТЫ формы. Qt такое не
        прощает: в консоли появлялись «Cannot set parent, new parent is in a
        different thread», «installEventFilter(): Cannot filter events for
        objects in a different thread», а затем программа падала
        («Cannot create children for a parent that is in a different thread»).
        Стресс-тест 15.10.2026 умирал на третьем запросе автозаполнения.

        :return: сколько инструментов переведено (для журнала и тестов).
        """
        routed = 0
        for name in GUI_TOOLS:
            tool = agent.registry.get(name)
            if tool is None or isinstance(tool.function, _GuiTool):
                continue
            tool.function = _GuiTool(self, name, tool.function)
            routed += 1
        if routed:
            logger.info("Инструментов формы в потоке интерфейса: %s", routed)
        return routed

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

    def _context(self) -> AgentContext:
        """
        Контекст агента: окно, тип договора, подтверждение, шаги.

        Отдельный метод, потому что контекст нужен в двух местах: агенту
        (инструменты формы) и подсказкам без вопроса (часть 4) — они
        смотрят ту же форму.
        """
        return AgentContext(
            window=self.window,
            contract_type=str(getattr(self.window, "CONTRACT_TYPE", "") or ""),
            confirm=self._confirm,
            on_activity=self._on_activity,
            open_diagnostics=self.request_diagnostics,
        )

    def request_diagnostics(self) -> bool:
        """
        Просьба инструмента `diagnose` открыть диалог диагностики.

        Вызывается из РАБОЧЕГО потока, поэтому диалог здесь не создаётся:
        просьба уходит сигналом в поток интерфейса
        (``diagnostics_requested`` → ``on_open_diagnostics``). Возвращается
        «передал»: инструмент по этому признаку говорит модели
        «диагностика открыта», а не «найдите пункт меню сами».
        """
        self.diagnostics_requested.emit()
        return True

    def _confirm(self, tool: Tool, arguments: Dict[str, Any]) -> bool:
        """
        Подтверждение опасного действия (задание, часть D.5).

        Для разбора текста (``parse_and_fill``) показывается диалог ПРЕВЬЮ:
        таблица «поле → значение → куда» с возможностью поправить значение
        (задание, часть 1, пункты B.1–B.3). Правки складываются в
        ``on_edit`` инструмента и попадают в аргументы вызова до заполнения.
        Остальные опасные действия спрашивают как прежде — «Да / Нет».

        ВАЖНО ПРО ПОТОКИ. Метод зовётся ИЗ РАБОЧЕГО ПОТОКА (там работает
        агент), а диалог — виджет: создавать его можно только в потоке
        интерфейса. Раньше диалог создавался прямо здесь, в рабочем потоке,
        да ещё и с родителем из главного потока — Qt отвечал
        «QObject::setParent: Cannot set parent, new parent is in a different
        thread» и РОНЯЛ процесс (проверено 15.10.2026: прогон 200 запросов
        трижды умирал на запросе про автозаполнение). Теперь диалог строит
        главный поток, а рабочий ЖДЁТ ответа — см. ``ask_in_gui_thread``.
        """
        return ask_in_gui_thread(self, tool, arguments)

    def _confirm_in_gui_thread(self, tool: Tool,
                               arguments: Dict[str, Any]) -> bool:
        """
        Показывает диалог подтверждения. Вызывается ТОЛЬКО из потока
        интерфейса (см. ``ask_in_gui_thread``).
        """
        parent = self.panel if self.panel is not None else self.window

        if tool.name == FILL_PREVIEW_TOOL:
            return self._confirm_with_preview(tool, arguments, parent)

        return ask_confirmation(tool, arguments, parent,
                                auto_answer=self.auto_confirm)

    def _confirm_with_preview(self, tool: Tool, arguments: Dict[str, Any],
                              parent: Any) -> bool:
        """
        Диалог превью разбора текста.

        Разбор считается ДО диалога и передаётся инструменту тем же текстом:
        диалог показывает результат ``parse_text``, а инструмент разбирает
        текст ещё раз у себя. Второй разбор нужен потому, что оператор мог
        поправить значения — правки уходят отдельным аргументом (``items``),
        и инструмент накладывает их на свежий разбор.
        """
        from ui.agent_fill_preview import preview_confirmation

        text = str(arguments.get("text") or "")
        if not text.strip():
            QMessageBox.information(
                parent, "Максимилиан",
                "В запросе нет текста с данными — заполнять нечего.",
            )
            return False

        use_dadata = bool(arguments.get("use_dadata", True))
        try:
            decision = preview_confirmation(
                text, parent, auto_answer=self.auto_confirm,
                use_dadata=use_dadata,
            )
        except Exception:  # noqa: BLE001 — диалог не должен ронять агента
            logger.exception(
                "Превью разбора не показалось — действие отменено"
            )
            return False

        if not decision.get("confirmed"):
            return False

        overrides = decision.get("overrides") or {}
        if overrides:
            #: Правки должны попасть в аргументы вызова ДО заполнения.
            #: Если инструмент объявил свой канал правок (``on_edit``) — идём
            #: через него; иначе кладём правки прямо в аргументы (``items`` и
            #: ``vehicles`` — ровно те имена, которые читает инструмент).
            if tool.on_edit is not None:
                try:
                    tool.on_edit(arguments)
                except Exception:  # noqa: BLE001 — правка не отменяет действие
                    logger.exception("Правки превью не применены")
            else:
                arguments.update(overrides)
            logger.info(
                "Превью разбора: правок оператора %s",
                len(overrides.get("items") or [])
                + len(overrides.get("vehicles") or []),
            )
        return True

    def _on_activity(self, message: str) -> None:
        """
        Шаг агента — вызывается ИЗ РАБОЧЕГО ПОТОКА.

        Здесь нельзя трогать панель: виджеты живут в потоке интерфейса.
        Раньше вызов уходил через ``QTimer.singleShot(0, lambda: …)``, но
        таймер, созданный в рабочем потоке, срабатывает В НЁМ ЖЕ — и панель
        менялась из чужого потока. Итог: два падения прогона из 200 запросов
        (`QObject::setParent: Cannot set parent, new parent is in a different
        thread` → `QObject::killTimer: Timers cannot be stopped from another
        thread` → процесс завершался с кодом 1). Теперь шаг уходит СИГНАЛОМ:
        Qt доставляет его в очередь главного потока и вызывает слот там.
        """
        if self.panel is None:
            return
        self.activity_reported.emit(str(message or ""))

    @pyqtSlot(str)
    def on_activity_reported(self, message: str) -> None:
        """Показывает шаг агента в панели — уже в потоке интерфейса."""
        if self.panel is None:
            return
        self.panel.set_status(message, True)

    @pyqtSlot()
    def on_confirmation_requested(self) -> None:
        """
        Показывает диалог подтверждения, который просит рабочий поток.

        Слот вызывается в потоке интерфейса (сигнал из рабочего потока Qt
        доставляет очередью), поэтому диалог создаётся там, где положено.
        Ответ уходит рабочему потоку через ``_PendingConfirmation``.
        """
        pending = self._confirmation
        if pending is None:
            return
        try:
            answer = self._confirm_in_gui_thread(pending.tool,
                                                 pending.arguments)
        except Exception:  # noqa: BLE001 — диалог не должен ронять агента
            logger.exception("Диалог подтверждения упал — действие отменено")
            answer = False
        finally:
            self._confirmation = None
        pending.answer_with(answer)

    @pyqtSlot()
    def on_gui_call_requested(self) -> None:
        """
        Выполняет инструмент формы, который просит рабочий поток.

        Слот вызывается в потоке интерфейса: только здесь можно читать и
        менять виджеты формы. Результат (или исключение) уходит рабочему
        потоку через ``_PendingGuiCall``.
        """
        pending = self._gui_call
        if pending is None:
            return
        try:
            pending.finish(pending.function(**pending.arguments))
        except Exception as e:  # noqa: BLE001 — ошибку увидит модель
            logger.exception("Инструмент в потоке интерфейса упал")
            pending.fail(e)
        finally:
            self._gui_call = None

    # ---------------------------------------------------------
    # Подсказки без вопроса (задание, часть 4)
    # ---------------------------------------------------------
    def _apply_proactive(self, values: Dict[str, Any]) -> None:
        """
        Включает или выключает подсказки без вопроса по настройкам.

        Подсказки проверяются таймером: смотреть форму на каждое изменение
        полей дорого, а оператору всё равно — он видит результат раз в
        несколько секунд. Пауза между САМИМИ подсказками задаётся
        настройкой (по умолчанию 5 минут, см. proactivity.SuggestionEngine).
        """
        enabled = bool(values.get("proactive", False))
        try:
            interval = float(values.get(
                "proactive_interval", proactivity.SUGGESTION_INTERVAL
            ))
        except (TypeError, ValueError):
            interval = proactivity.SUGGESTION_INTERVAL

        if self._suggestions is None:
            from core.agent.proactivity import SuggestionEngine

            self._suggestions = SuggestionEngine(enabled=enabled,
                                                 interval=interval)
        else:
            self._suggestions.configure(enabled, interval)

        if enabled:
            self._ensure_panel(
                AGENT_NAME, values["avatar"],
                int(values["max_history"]), int(values["max_tool_steps"]),
            )

        self._update_suggestion_timer()

    def _apply_tutorial(self, values: Dict[str, Any]) -> None:
        """
        Включает или выключает подсказки режима «Обучение».

        В отличие от проактивных подсказок (не чаще одной в 5 минут), режим
        «Обучение» подсказывает на КАЖДОМ шаге: интервал движка нулевой,
        а повторы гасит сам движок (тот же текст второй раз не показывается).
        """
        enabled = bool(values.get("tutorial_mode", False))

        if self._tutorial_engine is None:
            from core.agent.proactivity import SuggestionEngine

            self._tutorial_engine = SuggestionEngine(enabled=enabled, interval=0)
        else:
            self._tutorial_engine.configure(enabled, 0)

        if enabled:
            self._ensure_panel(
                AGENT_NAME, values["avatar"],
                int(values["max_history"]), int(values["max_tool_steps"]),
            )

        self._update_suggestion_timer()

    def _update_suggestion_timer(self) -> None:
        """Таймер подсказок работает, пока включён хотя бы один вид подсказок."""
        active = (
            (self._suggestions is not None and self._suggestions.enabled)
            or (self._tutorial_engine is not None and self._tutorial_engine.enabled)
        )
        if active:
            self._suggestion_timer.start()
        else:
            self._suggestion_timer.stop()

    def on_tutorial_toggled(self, enabled: bool) -> None:
        """
        Кнопка «Обучение» в панели: включает/выключает подсказки по шагам.

        Состояние пишется в настройки (`agent.tutorial_mode`) и применяется
        сразу — подсказки появляются без перезапуска.
        """
        self.settings.update_agent({"tutorial_mode": bool(enabled)})
        self._apply_tutorial(self.agent_settings())

    def _check_suggestions(self) -> None:
        """
        Проверяет, есть ли что подсказать, и показывает это в панели.

        Подсказка появляется ТОЛЬКО когда панель открыта: молчаливое
        всплывание поверх формы — именно та навязчивость, которой задание
        просит избегать. И только после паузы тишины (`_idle_timer`):
        подсказка посреди заполнения формы мешает.
        """
        if self.panel is None or not self.panel.isVisible():
            return
        if self._idle_timer.isActive():
            return

        self._offer_proactive()
        self._offer_tutorial()

    def _offer_proactive(self) -> None:
        """Показывает проактивную подсказку, если пора (часть 4)."""
        engine = self._suggestions
        if engine is None or not engine.enabled:
            return

        from core.agent.proactivity_tools import proactive_text

        try:
            text = proactive_text(self._context())
        except Exception as e:  # noqa: BLE001 — подсказка не должна ломать окно
            logger.warning("Подсказка не собрана: %s", e)
            return
        self._show_hint(engine.offer(text))

    def _offer_tutorial(self) -> None:
        """Показывает подсказку режима «Обучение» (система обучения)."""
        engine = self._tutorial_engine
        if engine is None or not engine.enabled:
            return

        from core.agent.tutorial_hints import tutorial_step_hint

        try:
            text = tutorial_step_hint(self._context())
        except Exception as e:  # noqa: BLE001 — подсказка не должна ломать окно
            logger.warning("Подсказка обучения не собрана: %s", e)
            return
        self._show_hint(engine.offer(text))

    def _show_hint(self, text: str) -> None:
        """Показывает готовую подсказку в панели (пустая строка — ничего)."""
        offer = str(text or "").strip()
        if not offer:
            return
        self.panel.append_message(offer, author="agent")
        self.panel.set_status("Подсказка", False)
        logger.info("Максимилиан: показана подсказка")

    def _on_operator_action(self, *_args: Any) -> None:
        """
        Оператор что-то нажал: подсказку откладываем до паузы в работе.

        Слот принимает лишние аргументы (сигнал журнала может передать
        их): `lambda` в связях запрещены (AGENTS.md § 5.1).
        """
        if self._any_hints_enabled():
            self._idle_timer.start()

    def _any_hints_enabled(self) -> bool:
        """Включён ли хотя бы один вид подсказок (проактивность или обучение)."""
        return (
            (self._suggestions is not None and self._suggestions.enabled)
            or (self._tutorial_engine is not None and self._tutorial_engine.enabled)
        )

    def _panel_call(self, func, *args) -> None:
        """
        УСТАРЕЛО и не используется: вызов панели из рабочего потока.

        Оставлено только чтобы не ломать внешние пробы, которые звали метод
        напрямую. Панель из рабочего потока НЕ меняется — для этого есть
        сигнал ``activity_reported`` (см. ``_on_activity``). Раньше здесь
        стоял ``QTimer.singleShot(0, lambda: …)``: таймер создавался в
        рабочем потоке и срабатывал в нём же, то есть виджет менялся из
        чужого потока (падение процесса, см. ``_on_activity``).
        """
        app = QApplication.instance()
        thread = getattr(app, "thread", None) if app is not None else None
        if thread is None or thread() is None or thread() is _current_thread():
            func(*args)
            return
        logger.warning(
            "Вызов панели из рабочего потока пропущен — меняйте интерфейс "
            "сигналом (AgentWidget.activity_reported)"
        )

    # ---------------------------------------------------------
    # Диалог
    # ---------------------------------------------------------
    def _ensure_worker(self) -> None:
        """
        Создаёт рабочий поток агента при первом вопросе.

        Поток живёт до выхода из программы: создавать его на каждый вопрос
        незачем (запросы идут по одному), а останавливать и ждать его надо
        явно — это и делает shutdown().
        """
        if self._thread is not None:
            return

        from PyQt5.QtCore import QThread

        self._thread = QThread()
        self._thread.setObjectName("agentThread")
        self._worker = AgentWorker()
        self._worker.moveToThread(self._thread)
        # Соединения «сигнал → слот» без lambda: замыкания на виджеты дают
        # цикл ссылок Python ↔ Qt (AGENTS.md § 5.1).
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.activity.connect(self._on_activity)
        self._thread.start()
        logger.debug("Максимилиан: рабочий поток запущен")

    def on_submit(self, message: str) -> None:
        """Отправляет сообщение агенту в рабочем потоке."""
        if self._busy:
            self.panel.set_status("Уже думаю над прошлым вопросом…", True)
            return
        if self._agent is None:
            values = self.agent_settings()
            self._load_history(int(values["max_history"]), int(values["max_tool_steps"]))
        else:
            #: Продолжение разговора из другого окна (часть 7): без сверки
            #: агент отвечал бы, не видя предыдущих вопросов оператора.
            self.sync_history()

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

        self._ensure_worker()

        self._busy = True
        self.panel.set_status("Думаю…", True)
        self._request = AgentRequest(self._agent, message)
        # Вызов уходит в поток агента: там запрос к GigaChat и работа с
        # инструментами, главный поток остаётся отзывчивым.
        QMetaObject.invokeMethod(
            self._worker, "process", Qt.QueuedConnection,
            Q_ARG(object, self._request),
        )

    def _on_finished(self, text: str, tool_names: List[str], limit: bool) -> None:
        """Ответ агента готов."""
        self._busy = False
        self._request = None
        self.panel.append_message(text, "agent", tool_names)
        status = "Достигнут лимит шагов — уточните запрос" if limit else ""
        self.panel.set_status(status, False)

    def _on_failed(self, error: str) -> None:
        """Запрос к агенту не удался: показываем причину."""
        self._busy = False
        self._request = None
        self.panel.set_status("", False)
        self.panel.append_message(f"Ошибка запроса: {error}", "system")

    def on_open_diagnostics(self) -> None:
        """
        Кнопка «🔧 Диагностика» в панели (задание, часть 3, пункт A.3).

        Открывает тот же диалог, что и «Справка → Диагностика»: одно окно
        диагностики на программу, а не два разных.
        """
        from ui.diagnostics_menu import window_diagnostics

        window_diagnostics(self.window)

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

        #: Разговор один на программу: перед показом панели подтягиваем то,
        #: что успели спросить в другом окне (задание, часть 7).
        self.sync_history()

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
        Горячая клавиша и слежение за размером окна.

        Два разных дела в одном фильтре:

          * окно изменило размер — панель (накладка) остаётся у правого края;
          * нажата горячая клавиша панели — открываем или закрываем её.

        Фильтр стоит на окне, поэтому вызывается и для нажатий внутри
        дочерних полей: сочетание с Ctrl в поле ввода всё равно должно
        работать.
        """
        host = getattr(self, "_host", None)
        if source is host and host is not None and event.type() == QEvent.Resize:
            if self.panel is not None and self.panel.isVisible():
                self._layout_panel()
            return super().eventFilter(source, event)

        if getattr(self, "_hotkey", None) is not None:
            if event.type() in (QEvent.ShortcutOverride, QEvent.KeyPress):
                if self._is_hotkey(event):
                    if event.type() == QEvent.ShortcutOverride:
                        # Сообщаем Qt, что сочетание наше: иначе его мог бы
                        # перехватить обработчик по умолчанию.
                        event.accept()
                    else:
                        self.handle_hotkey()
                        event.accept()
                    return True
        return super().eventFilter(source, event)

    def _is_hotkey(self, event) -> bool:
        """Совпадает ли нажатие с горячей клавишей панели."""
        pressed = QKeySequence(int(event.modifiers()) | int(event.key()))
        if pressed.isEmpty():
            return False
        # Сравниваем первые сочетания: у нажатия и у настройки они одни.
        return pressed[0] == self._hotkey[0]

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
    def close_floating(self) -> None:
        """
        Закрывает плавающего кота (баг #18).

        Отдельный метод, потому что кот — единственное окно Максимилиана
        верхнего уровня: иконка и панель живут внутри окна программы и
        умирают вместе с ним, а кот без явного close() остаётся на экране.
        Метод идемпотентен и безопасен, когда кота нет вовсе.
        """
        cat = self.floating
        if cat is None:
            return
        try:
            cat.close_cat()
        except RuntimeError:
            # Виджет уже разрушен вместе с окном — закрывать нечего.
            pass
        self.floating = None

    def shutdown(self) -> None:
        """
        Останавливает фоновую работу перед выходом.

        Вызывается из `force_close()` окна (и из `aboutToQuit` как страховка:
        метод идемпотентен).

        Что делает и чего НЕ делает:

          * ждёт рабочий поток агента и останавливает его — поток, доживший
            до завершения интерпретатора, роняет процесс (`0xC0000005`);
          * останавливает таймеры аватара и анимацию панели;
          * снимает подписки: горячая клавиша (фильтр событий) и страховка
            `aboutToQuit` — живая связь с методом Python на завершении тоже
            роняет процесс;
          * **не удаляет виджеты**. Их разрушает Qt вместе с окном-родителем
            в правильном порядке; попытка удалить их здесь (проверено)
            приводит к повторному удалению и падению `0xC0000005`.
        """
        if self._shutdown_done:
            return
        self._shutdown_done = True
        logger.info("Максимилиан: освобождаю ресурсы перед выходом")

        # Сначала замолкает запрос: его сигналы не должны попасть в окно,
        # которое вот-вот закроется.
        if self._request is not None:
            self._request.cancelled = True

        if self._thread is not None:
            logger.info("Максимилиан: останавливаю рабочий поток")
            try:
                self._thread.quit()
                if not self._thread.wait(5000):
                    logger.warning(
                        "Максимилиан: поток не завершился за 5 с — ждём дальше"
                    )
                    self._thread.wait()
            except RuntimeError:
                # Поток уже разрушен вместе с окном — ждать нечего.
                pass
            self._thread = None
            self._worker = None
        self._request = None

        try:
            self._animation.stop()
        except RuntimeError:
            pass

        # Плавающий кот закрывается по-настоящему (баг #18): своего
        # родителя у него нет — он окно верхнего уровня, и без явного
        # close() остаётся на экране после выхода из программы.
        self.close_floating()

        # Подсказки без вопроса (часть 4): таймеры обязаны замолчать до
        # закрытия окна — иначе они дёрнут закрытую панель.
        for timer in (self._suggestion_timer, self._idle_timer):
            try:
                timer.stop()
            except RuntimeError:
                pass

        for widget in (self.panel, self.icon_button, self.floating):
            avatar = getattr(widget, "avatar", None) if widget is not None else None
            if avatar is not None:
                avatar.stop_animation()

        if self._hotkey is not None:
            try:
                self.window.removeEventFilter(self)
            except (TypeError, RuntimeError):
                pass
            self._hotkey = None
            self.shortcut = None

        # Снимаем страховочную подписку: связь «сигнал приложения → метод
        # агента», дожившая до завершения интерпретатора, роняет процесс.
        app = QApplication.instance()
        if app is not None:
            try:
                app.aboutToQuit.disconnect(self.shutdown)
            except (TypeError, RuntimeError):
                pass

        set_context_window(None)
        logger.info("Максимилиан: фоновые ресурсы освобождены")


# ─────────────────────────────────────────────────────────────
# Установка в окно
# ─────────────────────────────────────────────────────────────

def _current_thread():
    """Текущий QThread (нужен, чтобы понять, в главном ли мы потоке)."""
    from PyQt5.QtCore import QThread

    return QThread.currentThread()


def _place_icon(window: QWidget, button: QWidget) -> bool:
    """
    Ставит иконку в ПРАВЫЙ НИЖНИЙ угол окна — в статус-бар.

    Статус-бар и есть нижняя полоса окна: ``addPermanentWidget`` кладёт
    кнопку в его правый край, а раскладка Qt сама резервирует под неё
    место. Поэтому иконка ничего не перекрывает — ни сообщения «Готово»,
    ни полосу прогресса, ни кнопки внизу формы, ни таблицы вкладок (в
    отличие от накладки поверх содержимого), — и остаётся на месте, пока
    оператор таскает плавающего кота.

    Возвращает False, если статус-бара нет (нестандартный каркас окна):
    тогда вызывающий код пробует шапку — агент должен быть доступен.
    """
    status = getattr(window, "statusBar", None)
    if callable(status):
        bar = status()
        if bar is not None:
            bar.addPermanentWidget(button)
            if not bar.isVisible():
                # Бар мог быть создан уже после показа окна: сам по себе он
                # остаётся скрытым, и иконка была бы не видна. Показываем —
                # окна проекта статус-бар не прячут.
                bar.show()
            return True
    return False


def _place_icon_in_header(window: QWidget, button: QWidget) -> bool:
    """
    Запасной вариант: иконка встаёт в шапку окна (перед кнопками действий).

    Нужен только окнам без статус-бара. Шапка есть у всех окон (QFrame с
    именем «appHeader»), но её раскладка не сохраняется атрибутом, поэтому
    ищем её по имени объекта. Не нашли — False, и вызывающий код пишет
    предупреждение в лог.
    """
    try:
        header = window.findChild(QFrame, "appHeader")
        if header is None:
            return False
        layout = header.layout()
        if layout is None:
            return False
        layout.insertWidget(max(0, layout.count() - 1), button)
        return True
    except Exception as e:  # noqa: BLE001 — не нашли шапку, сообщим в лог
        logger.warning("Иконка Максимилиана не встала в шапку: %s", e)
        return False


def _status_bar_height(window: QWidget) -> int:
    """
    Высота статус-бара окна (0 — бара нет).

    Нужна плавающему коту: в правом краю статус-бара живёт иконка агента,
    и кот не должен её закрывать (задание, часть F.3). Пока раскладка не
    прошла, ``height()`` ещё не настоящая — берём запрошенную высоту.
    """
    status = getattr(window, "statusBar", None)
    if not callable(status):
        return 0
    bar = status()
    if bar is None:
        return 0
    return max(0, int(bar.height()), int(bar.sizeHint().height()))


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
    window.agent_widget = None


def close_floating_cats(app: Optional[Any] = None) -> int:
    """
    Закрывает всех плавающих котов приложения (баг #18).

    Страховка на выходе из программы: кот — окно верхнего уровня, и без
    явного закрытия он остаётся на экране, даже когда окно программы уже
    закрыто. Обычно котов закрывает `AgentWidget.shutdown()` из
    `force_close()` окна, но если окно до этого не закрывали (или кот был
    создан в другом окне), кот переживёт программу.

    :param app: приложение Qt. По умолчанию берётся текущее; если Qt не
        поднят вовсе, функция молча отвечает нулём (её зовут на выходе, и
        падать там нельзя).
    :return: сколько котов закрыто
    """
    if app is None:
        app = QApplication.instance()
    if app is None:
        return 0

    closed = 0
    for widget in app.topLevelWidgets():
        if not isinstance(widget, FloatingCat):
            continue
        try:
            widget.close_cat()
            closed += 1
        except RuntimeError:
            # Виджет уже разрушен — закрывать нечего.
            continue

    for widget in app.allWidgets():
        agent = getattr(widget, "agent_widget", None)
        if agent is None:
            continue
        try:
            agent.close_floating()
        except Exception as e:  # noqa: BLE001 — выход не должен падать
            logger.warning("Плавающий кот не закрыт: %s", e)

    if closed:
        logger.info("Плавающих котов закрыто: %s", closed)
    return closed


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
    "AgentRequest",
    "AgentWidget",
    "AgentWorker",
    "ConfirmationDialog",
    "FloatingCat",
    "active_form_window",
    "agent_should_open_on_start",
    "ask_confirmation",
    "close_floating_cats",
    "default_client_factory",
    "install_agent",
    "install_agent_safely",
    "reset_startup_panel",
    "uninstall_agent",
]
