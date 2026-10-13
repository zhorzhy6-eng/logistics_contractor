#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Аватар Максимилиана: кот, нарисованный кодом (задание, часть D.2).

Почему рисунок, а не PNG/SVG-файлы: набор из четырёх образов в трёх
размерах — это двенадцать картинок, которые надо держать в репозитории и
не забыть перерисовать под тёмную тему. Кот здесь рисуется QPainter-ом:
масштабируется на любой размер (32 / 64 / 96 px и retina), не зависит от
темы и одинаково выглядит в сборке PyInstaller.

Образы (совпадают с core/agent/personas.py):

  * ``cat_scientist`` — очки и лупа;
  * ``cat_logist``    — жилет и планшет;
  * ``cat_keeper``    — спит (глаза закрыты);
  * ``neutral``       — просто кот, без атрибутов.

Анимация: ``CatAvatar`` мигает раз в 5–10 секунд и умеет «думать» —
во время запроса к GigaChat глаза широко открыты. Таймеры останавливаются
при скрытии виджета: анимация не должна крутиться в фоне.
"""

import logging
import random
from typing import Optional

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt5.QtWidgets import QWidget

from core.agent.personas import DEFAULT_PERSONA

logger = logging.getLogger("ui.agent_avatar")

#: Цвета кота. Фиксированные (не из темы): кот — персонаж, а не элемент
#: оформления. Контур тёмный, поэтому он читается и на светлом, и на
#: тёмном фоне.
FUR = QColor("#E8A24A")
FUR_DARK = QColor("#C97F2C")
MUZZLE = QColor("#FBEEDC")
EYE = QColor("#2B2118")
OUTLINE = QColor("#4A3520")
GLASS = QColor("#7FD3F7")
VEST = QColor("#3F7FBF")
TABLET = QColor("#DCE6F2")
METAL = QColor("#B9C4D2")

#: Границы паузы между миганиями (задание, часть D.1.3: раз в 5–10 секунд).
BLINK_MIN_MS = 5000
BLINK_MAX_MS = 10000
#: Сколько держится закрытый глаз.
BLINK_HOLD_MS = 130


def avatar_keys() -> tuple:
    """Ключи образов, у которых есть свой рисунок (для настроек)."""
    return ("cat_scientist", "cat_logist", "cat_keeper", "neutral")


def _norm(key: str) -> str:
    """Ключ образа; неизвестный — образ по умолчанию."""
    value = str(key or "").strip()
    return value if value in avatar_keys() else DEFAULT_PERSONA


def paint_cat(
    painter: QPainter,
    size: float,
    persona_key: str = DEFAULT_PERSONA,
    eyes: str = "open",
    rect: Optional[QRectF] = None,
) -> None:
    """
    Рисует кота в квадрате ``rect`` (по умолчанию — квадрат ``size``).

    :param eyes: «open» — глаза открыты, «closed» — закрыты (мигание или
        спящий хранитель), «wide» — широко открыты (кот думает).
    """
    rect = rect or QRectF(0, 0, size, size)
    unit = min(rect.width(), rect.height()) / 100.0
    persona = _norm(persona_key)

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.translate(rect.left(), rect.top())
    # Работаем в системе координат 100×100: все числа ниже — «проценты».
    painter.scale(unit, unit)

    if persona == "cat_keeper":
        eyes = "closed"

    _paint_head(painter, persona, eyes)
    if persona == "cat_scientist":
        _paint_glasses(painter)
        _paint_magnifier(painter)
    elif persona == "cat_logist":
        _paint_vest(painter)
        _paint_tablet(painter)
    elif persona == "cat_keeper":
        _paint_paws_and_z(painter)

    painter.restore()


def _outlined(painter: QPainter, fill: QColor, width: float = 2.0) -> None:
    """Кисть с заливкой и тёмным контуром — общий стиль рисунка."""
    painter.setBrush(QBrush(fill))
    painter.setPen(QPen(OUTLINE, width))


def _paint_head(painter: QPainter, persona: str, eyes: str) -> None:
    """Голова, уши, морда, глаза, нос и усы."""
    # ── Уши ──
    _outlined(painter, FUR)
    for sign in (-1, 1):
        path = QPainterPath()
        base_x = 50 + sign * 24
        path.moveTo(base_x - sign * 11, 34)
        path.lineTo(base_x + sign * 2, 10)
        path.lineTo(base_x + sign * 12, 30)
        path.closeSubpath()
        painter.drawPath(path)
        # Внутренняя часть уха
        painter.setBrush(QBrush(FUR_DARK))
        inner = QPainterPath()
        inner.moveTo(base_x - sign * 5, 31)
        inner.lineTo(base_x + sign * 1, 18)
        inner.lineTo(base_x + sign * 7, 29)
        inner.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.drawPath(inner)
        _outlined(painter, FUR)

    # ── Голова ──
    painter.drawEllipse(QRectF(16, 22, 68, 62))

    # ── Морда (светлое пятно) ──
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(MUZZLE))
    painter.drawEllipse(QRectF(31, 52, 38, 26))

    # ── Глаза ──
    painter.setPen(QPen(OUTLINE, 1.6))
    for sign in (-1, 1):
        cx = 50 + sign * 14
        if eyes == "closed":
            painter.setBrush(Qt.NoBrush)
            # Целые аргументы: у drawArc(float-перегрузка принимает QRectF
            # и углы в 1/16 градуса, что читается хуже, чем дуга «на глаз».
            painter.drawArc(int(cx - 7), 42, 14, 10, 200 * 16, 140 * 16)
            continue
        radius = 7.6 if eyes == "wide" else 6.0
        painter.setBrush(QBrush(EYE))
        painter.drawEllipse(QRectF(cx - radius, 47 - radius, radius * 2, radius * 2))
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(cx - radius + 1.6, 47 - radius + 1.4, 2.6, 2.6))
        painter.setPen(QPen(OUTLINE, 1.6))

    # ── Нос и рот ──
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor("#C4626B")))
    nose = QPainterPath()
    nose.moveTo(46, 62)
    nose.lineTo(54, 62)
    nose.lineTo(50, 67)
    nose.closeSubpath()
    painter.drawPath(nose)

    painter.setPen(QPen(OUTLINE, 1.4))
    painter.setBrush(Qt.NoBrush)
    painter.drawArc(43, 64, 14, 12, 200 * 16, 140 * 16)

    # ── Усы ──
    painter.setPen(QPen(OUTLINE, 1.2))
    for sign in (-1, 1):
        for offset, length in ((0, 16), (4, 14), (8, 12)):
            painter.drawLine(
                QPointF(50 + sign * 9, 62 + offset * 0.35),
                QPointF(50 + sign * (9 + length), 58 + offset),
            )


def _paint_glasses(painter: QPainter) -> None:
    """Очки учёного кота."""
    painter.setBrush(QBrush(QColor(127, 211, 247, 70)))
    painter.setPen(QPen(METAL, 2.2))
    painter.drawEllipse(QRectF(28, 39, 18, 16))
    painter.drawEllipse(QRectF(54, 39, 18, 16))
    painter.drawLine(QPointF(46, 47), QPointF(54, 47))
    painter.drawLine(QPointF(28, 46), QPointF(19, 44))
    painter.drawLine(QPointF(72, 46), QPointF(81, 44))


def _paint_magnifier(painter: QPainter) -> None:
    """Лупа в лапе (правый нижний угол)."""
    painter.setBrush(QBrush(QColor(127, 211, 247, 90)))
    painter.setPen(QPen(OUTLINE, 2.6))
    painter.drawEllipse(QRectF(66, 66, 26, 26))
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(OUTLINE, 3.4))
    painter.drawLine(QPointF(88, 89), QPointF(96, 97))


def _paint_vest(painter: QPainter) -> None:
    """Сигнальный жилет кота-логиста."""
    painter.setPen(QPen(OUTLINE, 1.8))
    painter.setBrush(QBrush(VEST))
    path = QPainterPath()
    path.moveTo(30, 84)
    path.lineTo(30, 74)
    path.lineTo(70, 74)
    path.lineTo(70, 84)
    path.closeSubpath()
    painter.drawPath(path)

    painter.setBrush(QBrush(QColor("#F2F7A0")))
    painter.setPen(Qt.NoPen)
    for x in (36, 48, 60):
        painter.drawRect(QRectF(x, 76, 4, 7))
    painter.setPen(QPen(OUTLINE, 1.8))


def _paint_tablet(painter: QPainter) -> None:
    """Планшет в лапах."""
    painter.setPen(QPen(OUTLINE, 1.8))
    painter.setBrush(QBrush(TABLET))
    painter.drawRoundedRect(QRectF(60, 70, 30, 22), 3, 3)
    painter.setPen(QPen(QColor("#8FA3BF"), 1.4))
    for index, width in enumerate((18, 14, 16)):
        painter.drawLine(
            QPointF(65, 76 + index * 5), QPointF(65 + width, 76 + index * 5)
        )


def _paint_paws_and_z(painter: QPainter) -> None:
    """Спящий хранитель: лапы и «z-z-z»."""
    painter.setPen(QPen(OUTLINE, 1.6))
    painter.setBrush(QBrush(MUZZLE))
    painter.drawEllipse(QRectF(52, 78, 22, 12))

    painter.setPen(QPen(OUTLINE, 2.0))
    painter.setBrush(Qt.NoBrush)
    for index, scale in enumerate((1.0, 0.75, 0.55)):
        x = 74 + index * 8
        y = 68 - index * 9
        painter.drawLine(QPointF(x, y), QPointF(x + 6 * scale, y))
        painter.drawLine(QPointF(x + 6 * scale, y), QPointF(x, y + 6 * scale))
        painter.drawLine(QPointF(x, y + 6 * scale), QPointF(x + 6 * scale, y + 6 * scale))


def avatar_pixmap(
    size: int = 64,
    persona_key: str = DEFAULT_PERSONA,
    eyes: str = "open",
    device_ratio: float = 1.0,
) -> QPixmap:
    """Готовый рисунок кота (для иконок, подсказок и скриншотов)."""
    ratio = max(1.0, float(device_ratio))
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        paint_cat(painter, size * ratio, persona_key, eyes)
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def avatar_icon(size: int = 32, persona_key: str = DEFAULT_PERSONA) -> QIcon:
    """Иконка кота для кнопки в углу окна."""
    return QIcon(avatar_pixmap(size, persona_key))


class CatAvatar(QWidget):
    """
    Живой аватар: статичный рисунок плюс мигание (задание, часть D.2).

    Виджет не знает, где он стоит: размер задаётся ``set_avatar_size``,
    образ — ``set_persona``, «думает» — ``set_thinking``. Таймер мигания
    останавливается, когда виджет скрыт: анимация в фоне не нужна.
    """

    #: Кот мигнул (нужно тестам: анимация проверяется без ожидания 10 секунд).
    blinked = pyqtSignal()

    def __init__(self, size: int = 64, persona_key: str = DEFAULT_PERSONA,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._size = max(16, int(size))
        self._persona = _norm(persona_key)
        self._eyes = "open"
        self._thinking = False
        self._blink_timer = QTimer(self)
        self._blink_timer.setSingleShot(True)
        self._blink_timer.timeout.connect(self._close_eyes)
        self._open_timer = QTimer(self)
        self._open_timer.setSingleShot(True)
        self._open_timer.timeout.connect(self._open_eyes)

        self.setFixedSize(QSize(self._size, self._size))
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._schedule_blink()

    # ---------------------------------------------------------
    # Свойства
    # ---------------------------------------------------------
    def set_avatar_size(self, size: int) -> None:
        """Меняет размер рисунка (32 — иконка, 64 — панель, 96 — плавающий)."""
        self._size = max(16, int(size))
        self.setFixedSize(QSize(self._size, self._size))
        self.update()

    def avatar_size(self) -> int:
        """Текущий размер рисунка."""
        return self._size

    def set_persona(self, persona_key: str) -> None:
        """Меняет образ (ключи — core/agent/personas.py)."""
        self._persona = _norm(persona_key)
        self.update()

    def persona(self) -> str:
        """Текущий образ."""
        return self._persona

    def set_thinking(self, thinking: bool) -> None:
        """
        «Думает»: глаза широко открыты, мигание приостановлено.

        Пока агент ждёт ответ, аватар должен выглядеть живым, а не мигать
        вразнобой с индикатором «думаю…».
        """
        self._thinking = bool(thinking)
        if self._thinking:
            self._blink_timer.stop()
            self._open_timer.stop()
            self._eyes = "wide"
        else:
            self._eyes = "open"
            self._schedule_blink()
        self.update()

    def is_thinking(self) -> bool:
        """Ждёт ли аватар ответ модели."""
        return self._thinking

    def eyes_state(self) -> str:
        """
        Состояние глаз: open / closed / wide (нужно тестам).

        У спящего хранителя глаза закрыты всегда — это его образ, а не
        мигание: так же считает и рисование (см. paint_cat).
        """
        if self._persona == "cat_keeper" and self._eyes != "wide":
            return "closed"
        return self._eyes

    def blink_now(self) -> None:
        """Мигает немедленно (тесты и ручная проверка анимации)."""
        if not self._thinking:
            self._close_eyes()
            self._schedule_blink()

    # ---------------------------------------------------------
    # Анимация
    # ---------------------------------------------------------
    def _schedule_blink(self) -> None:
        """Планирует следующее мигание через 5–10 секунд."""
        if self._thinking or not self.isVisible():
            return
        self._blink_timer.start(random.randint(BLINK_MIN_MS, BLINK_MAX_MS))

    def _close_eyes(self) -> None:
        """Закрывает глаза на мгновение."""
        self._eyes = "closed"
        self.update()
        self._open_timer.start(BLINK_HOLD_MS)
        self.blinked.emit()

    def _open_eyes(self) -> None:
        """Открывает глаза и планирует следующее мигание."""
        self._eyes = "open"
        self.update()
        self._schedule_blink()

    def showEvent(self, event) -> None:
        """Показ виджета — возобновляем мигание."""
        super().showEvent(event)
        self._schedule_blink()

    def hideEvent(self, event) -> None:
        """Скрытие виджета — мигание останавливается."""
        self._blink_timer.stop()
        self._open_timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 — имя метода Qt
        """Рисует кота текущего размера и состояния."""
        painter = QPainter(self)
        try:
            paint_cat(painter, self._size, self._persona, self._eyes)
        finally:
            painter.end()


__all__ = [
    "BLINK_HOLD_MS",
    "BLINK_MAX_MS",
    "BLINK_MIN_MS",
    "CatAvatar",
    "avatar_icon",
    "avatar_keys",
    "avatar_pixmap",
    "paint_cat",
]
